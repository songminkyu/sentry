import styled from '@emotion/styled';

import {space} from 'sentry/styles/space';
import type {WidgetTemplate} from 'sentry/views/dashboards/widgetLibrary/data';
import {getWidgetIcon} from 'sentry/views/dashboards/widgetLibrary/widgetCard';

interface CardProps {
  iconColor: string;
  widget: WidgetTemplate;
}

export function Card({widget, iconColor}: CardProps) {
  const {title, description, displayType} = widget;
  const Icon = getWidgetIcon(displayType);

  return (
    <Container>
      <IconWrapper backgroundColor={iconColor}>
        <Icon color="white" />
      </IconWrapper>
      <Information>
        <Heading>{title}</Heading>
        <SubHeading>{description}</SubHeading>
      </Information>
    </Container>
  );
}

const Container = styled('div')`
  display: flex;
  flex-direction: row;
  gap: ${space(1)};
`;

const Information = styled('div')`
  display: flex;
  flex-direction: column;
`;

const Heading = styled('div')`
  font-size: ${p => p.theme.fontSize.lg};
  font-weight: ${p => p.theme.fontWeight.normal};
  margin-bottom: 0;
  color: ${p => p.theme.gray500};
`;

const SubHeading = styled('small')`
  color: ${p => p.theme.subText};
`;

const IconWrapper = styled('div')<{backgroundColor: string}>`
  display: flex;
  justify-content: center;
  align-items: center;
  padding: ${space(1)};
  min-width: 40px;
  height: 40px;
  border-radius: ${p => p.theme.borderRadius};
  background: ${p => p.backgroundColor};
`;
